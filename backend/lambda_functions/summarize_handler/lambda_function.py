"""
summarize_handler
------------------
Triggered by: POST /summarize  (API Gateway -> Lambda proxy integration)

Expected request body (JSON):
{
    "student_id": "shreya01",
    "note_id": "a1b2c3d4-..."
}

What it does:
1. Looks up the note's location in DynamoDB (written earlier by upload_handler)
2. Reads the actual file content from S3
3. Sends the text to Claude (via Bedrock) asking for a summary
4. Saves the summary back to DynamoDB
5. Returns the summary to the caller
"""

import json
import os

import boto3

s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
bedrock_client = boto3.client("bedrock-runtime")

NOTES_BUCKET = os.environ["NOTES_BUCKET"]
TABLE_NAME = os.environ["TABLE_NAME"]

# See Module 7 notes: if this exact model ID isn't invokable on-demand in
# your account/region, switch this environment variable to the inference
# profile ARN instead (e.g. "arn:aws:bedrock:us-east-1:<account>:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0").
MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-haiku-4-5-20251001-v1:0"
)

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


def invoke_model(prompt, max_tokens=1000):
    """
    Calls Amazon Nova via Bedrock. Nova uses a different request/response
    shape than Claude's Messages API:
      - request:  {"messages": [...], "inferenceConfig": {"maxTokens": ...}}
      - response: response["output"]["message"]["content"][0]["text"]
    (Switched from Claude to Nova because of a Marketplace billing
    restriction on this AWS account - see README. Nova is Amazon's own
    model, so it doesn't require an AWS Marketplace subscription.)
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


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        student_id = body.get("student_id")
        note_id = body.get("note_id")

        if not student_id or not note_id:
            return response(400, {"error": "student_id and note_id are both required"})

        # Step 1: look up where this note actually lives in S3.
        # This is exactly why we saved s3Key back in upload_handler -
        # the frontend never needs to know S3 paths, only IDs.
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

        # v1 limitation: only .txt files are supported for now.
        # PDF support requires a Lambda layer (pypdf) - a planned future step.
        if not filename.lower().endswith(".txt"):
            return response(400, {
                "error": "Only .txt files are supported for summarization right now. "
                         "PDF support is planned as a future enhancement."
            })

        # Step 2: read the actual note content from S3.
        s3_object = s3_client.get_object(Bucket=NOTES_BUCKET, Key=s3_key)
        note_text = s3_object["Body"].read().decode("utf-8")

        # Step 3: build a clear, constrained prompt. Being specific about
        # length and style keeps Claude's output predictable and reusable
        # in the UI, rather than getting a wildly different format each time.
        prompt = (
            "You are a helpful study assistant. Summarize the following "
            "student notes into 5-7 concise bullet points covering the key "
            "concepts. Do not add information that isn't in the notes.\n\n"
            f"NOTES:\n{note_text}"
        )

        summary_text = invoke_model(prompt, max_tokens=500)

        # Step 4: save the summary so it doesn't need regenerating every time
        # the student revisits this note.
        table.put_item(
            Item={
                "PK": f"USER#{student_id}",
                "SK": f"SUMMARY#{note_id}",
                "content": {"summary": summary_text},
                "status": "ready",
            }
        )

        return response(200, {
            "note_id": note_id,
            "summary": summary_text,
        })

    except Exception as e:
        print(f"ERROR in summarize_handler: {str(e)}")
        return response(500, {"error": "Something went wrong while generating the summary"})