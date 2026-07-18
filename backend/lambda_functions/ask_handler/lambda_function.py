"""
ask_handler
-----------
Triggered by: POST /ask  (API Gateway -> Lambda proxy integration)

Expected request body (JSON):
{
    "student_id": "shreya01",
    "note_id": "a1b2c3d4-...",
    "question": "What is the difference between a semaphore and a mutex?"
}

What it does:
1. Looks up the note's location in DynamoDB (same pattern as summarize_handler)
2. Reads the note content from S3
3. Sends Claude BOTH the note content and the question together
   (this is "retrieval-augmented prompting" - grounding the answer in
   the actual source document instead of Claude's general knowledge)
4. Saves the Q&A pair to DynamoDB as history
5. Returns the answer to the caller
"""

import json
import os
from datetime import datetime, timezone

import boto3

s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
bedrock_client = boto3.client("bedrock-runtime")

NOTES_BUCKET = os.environ["NOTES_BUCKET"]
TABLE_NAME = os.environ["TABLE_NAME"]

# This should be the SAME inference profile ARN used in summarize_handler -
# see Module 8 for why a direct model ID alone isn't enough.
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


def invoke_claude(prompt, max_tokens=600):
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [
            {"role": "user", "content": prompt}
        ],
    }

    bedrock_response = bedrock_client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(request_body),
    )

    response_body = json.loads(bedrock_response["body"].read())
    return response_body["content"][0]["text"]


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        student_id = body.get("student_id")
        note_id = body.get("note_id")
        question = body.get("question")

        if not student_id or not note_id or not question:
            return response(400, {
                "error": "student_id, note_id, and question are all required"
            })

        # Step 1: same lookup pattern as summarize_handler - find where
        # this note actually lives.
        lookup = table.get_item(
            Key={
                "PK": f"USER#{student_id}",
                "SK": f"NOTE#{note_id}",
            }
        )

        item = lookup.get("Item")
        if not item:
            return response(404, {"error": "Note not found. Check the student_id and note_id."})

        s3_key = item["content"]["s3Key"]
        filename = item["content"]["filename"]

        if not filename.lower().endswith(".txt"):
            return response(400, {
                "error": "Only .txt files are supported right now. "
                         "PDF support is planned as a future enhancement."
            })

        # Step 2: read the note content - this is the "retrieval" half of
        # retrieval-augmented prompting. We're not searching a database of
        # embeddings; we're directly fetching the one document the student
        # is asking about.
        s3_object = s3_client.get_object(Bucket=NOTES_BUCKET, Key=s3_key)
        note_text = s3_object["Body"].read().decode("utf-8")

        # Step 3: the prompt explicitly instructs Claude to answer ONLY from
        # the provided notes, and to say so honestly if the answer isn't
        # there. This one instruction is what prevents the model from
        # quietly making things up (hallucinating) when the notes don't
        # cover the question.
        prompt = (
            "You are a study assistant helping a student understand their own notes. "
            "Answer the question using ONLY the information in the notes below. "
            "If the notes don't contain enough information to answer, say so honestly "
            "instead of guessing.\n\n"
            f"NOTES:\n{note_text}\n\n"
            f"QUESTION: {question}"
        )

        answer_text = invoke_claude(prompt, max_tokens=600)

        # Step 4: save this Q&A pair as history. Note the SK uses a
        # timestamp, so multiple questions about the same note each get
        # their own item instead of overwriting each other.
        timestamp = datetime.now(timezone.utc).isoformat()
        table.put_item(
            Item={
                "PK": f"USER#{student_id}",
                "SK": f"QA#{timestamp}",
                "content": {
                    "note_id": note_id,
                    "question": question,
                    "answer": answer_text,
                },
            }
        )

        return response(200, {
            "note_id": note_id,
            "question": question,
            "answer": answer_text,
        })

    except Exception as e:
        print(f"ERROR in ask_handler: {str(e)}")
        return response(500, {"error": "Something went wrong while answering the question"})