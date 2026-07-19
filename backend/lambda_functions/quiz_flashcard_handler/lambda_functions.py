"""
quiz_flashcard_handler
-----------------------
Triggered by TWO routes, both pointing at this same function:
  POST /quiz        -> generates multiple-choice quiz questions
  POST /flashcards   -> generates front/back flashcards

Expected request body (JSON), same shape for both routes:
{
    "student_id": "shreya01",
    "note_id": "a1b2c3d4-..."
}

What it does:
1. Figures out which route was called (quiz vs flashcards) from the
   API Gateway event
2. Looks up + reads the note, same pattern as summarize_handler/ask_handler
3. Asks Claude to generate content AND return it as strict JSON
4. Parses that JSON safely (AI output is not always perfectly clean)
5. Saves the result to DynamoDB and returns it
"""

import json
import os
import re

import boto3

s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
bedrock_client = boto3.client("bedrock-runtime")

NOTES_BUCKET = os.environ["NOTES_BUCKET"]
TABLE_NAME = os.environ["TABLE_NAME"]
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]

table = dynamodb.Table(TABLE_NAME)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "OPTIONS,POST",
}


def response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body_dict),
    }


def invoke_model(prompt, max_tokens=1200):
    """
    Calls Amazon Nova via Bedrock (see summarize_handler for why we
    switched from Claude to Nova). Nova's request/response shape differs
    from Claude's Messages API.
    """
    request_body = {
        "messages": [
            {"role": "user", "content": [{"text": prompt}]}
        ],
        "inferenceConfig": {"maxTokens": max_tokens},
    }

    bedrock_response = bedrock_client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(request_body),
    )

    response_body = json.loads(bedrock_response["body"].read())
    return response_body["output"]["message"]["content"][0]["text"]


def extract_json(raw_text):
    """
    Claude sometimes wraps JSON in ```json ... ``` code fences, or adds a
    short sentence before/after the JSON even when asked not to. This pulls
    out just the {...} or [...] block so json.loads() doesn't choke on
    surrounding text.
    """
    # Strip markdown code fences if present.
    cleaned = re.sub(r"```json|```", "", raw_text).strip()

    # Find the first '{' or '[' and the last matching '}' or ']'.
    start_candidates = [i for i in [cleaned.find("{"), cleaned.find("[")] if i != -1]
    if not start_candidates:
        raise ValueError("No JSON object or array found in the model's response")
    start = min(start_candidates)

    end_candidates = [i for i in [cleaned.rfind("}"), cleaned.rfind("]")] if i != -1]
    end = max(end_candidates) + 1

    return json.loads(cleaned[start:end])


def get_note_text(student_id, note_id):
    """Shared lookup logic used by both the quiz and flashcard paths."""
    lookup = table.get_item(
        Key={
            "PK": f"USER#{student_id}",
            "SK": f"NOTE#{note_id}",
        }
    )

    item = lookup.get("Item")
    if not item:
        return None, None

    s3_key = item["content"]["s3Key"]
    filename = item["content"]["filename"]

    if not filename.lower().endswith(".txt"):
        return None, "unsupported_type"

    s3_object = s3_client.get_object(Bucket=NOTES_BUCKET, Key=s3_key)
    note_text = s3_object["Body"].read().decode("utf-8")
    return note_text, None


def generate_quiz(note_text):
    prompt = (
        "You are a study assistant. Based on the notes below, create exactly "
        "5 multiple-choice quiz questions to test understanding of the key "
        "concepts.\n\n"
        "Respond with ONLY valid JSON, no other text, in exactly this shape:\n"
        '{"quiz": [{"question": "...", "options": ["A text", "B text", "C text", "D text"], '
        '"correct_answer": "A"}]}\n\n'
        f"NOTES:\n{note_text}"
    )
    raw = invoke_model(prompt, max_tokens=1200)
    parsed = extract_json(raw)
    return parsed["quiz"]


def generate_flashcards(note_text):
    prompt = (
        "You are a study assistant. Based on the notes below, create exactly "
        "8 flashcards covering the key terms and concepts. Each flashcard has "
        "a short 'front' (a term or question) and a concise 'back' "
        "(the definition or answer).\n\n"
        "Respond with ONLY valid JSON, no other text, in exactly this shape:\n"
        '{"flashcards": [{"front": "...", "back": "..."}]}\n\n'
        f"NOTES:\n{note_text}"
    )
    raw = invoke_model(prompt, max_tokens=1200)
    parsed = extract_json(raw)
    return parsed["flashcards"]


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        student_id = body.get("student_id")
        note_id = body.get("note_id")

        if not student_id or not note_id:
            return response(400, {"error": "student_id and note_id are both required"})

        # Figure out which route triggered this - API Gateway proxy
        # integration puts the matched path in event["path"] (or
        # event["resource"] / event["rawPath"] depending on API type).
        path = event.get("path") or event.get("rawPath") or ""
        is_flashcards_request = "flashcard" in path

        note_text, error_flag = get_note_text(student_id, note_id)

        if error_flag == "unsupported_type":
            return response(400, {
                "error": "Only .txt files are supported right now. "
                         "PDF support is planned as a future enhancement."
            })
        if note_text is None:
            return response(404, {"error": "Note not found. Check the student_id and note_id."})

        if is_flashcards_request:
            flashcards = generate_flashcards(note_text)

            table.put_item(
                Item={
                    "PK": f"USER#{student_id}",
                    "SK": f"CARD#{note_id}",
                    "content": {"flashcards": flashcards},
                    "status": "ready",
                }
            )
            return response(200, {"note_id": note_id, "flashcards": flashcards})

        else:
            quiz = generate_quiz(note_text)

            table.put_item(
                Item={
                    "PK": f"USER#{student_id}",
                    "SK": f"QUIZ#{note_id}",
                    "content": {"quiz": quiz},
                    "status": "ready",
                }
            )
            return response(200, {"note_id": note_id, "quiz": quiz})

    except (ValueError, KeyError) as e:
        # These specifically mean Claude's response didn't parse as expected
        # JSON - worth a distinct message since it points at a prompt issue,
        # not an AWS infrastructure issue.
        print(f"PARSE ERROR in quiz_flashcard_handler: {str(e)}")
        return response(500, {"error": "The AI response couldn't be understood. Please try again."})

    except Exception as e:
        print(f"ERROR in quiz_flashcard_handler: {str(e)}")
        return response(500, {"error": "Something went wrong while generating content"})