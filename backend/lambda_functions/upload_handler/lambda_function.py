"""
upload_handler
--------------
Triggered by: POST /upload  (API Gateway -> Lambda proxy integration)

Expected request body (JSON):
{
    "student_id": "shreya01",
    "filename": "os-notes.pdf",
    "file_content_base64": "<base64 encoded file bytes>"
}

What it does:
1. Validates the incoming request
2. Decodes the Base64 file content back to raw bytes
3. Uploads the file to the private notes S3 bucket
4. Writes a record of the upload into DynamoDB
5. Returns the generated note_id so the frontend can reference it later
"""

import json
import base64
import uuid
import os
from datetime import datetime, timezone

import boto3

# boto3 clients are created outside the handler function so Lambda can
# reuse them across warm invocations instead of reconnecting every time.
s3_client = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

# Environment variables let us change bucket/table names without editing
# code - we'll set these in the Lambda console (or template) in a later step.
NOTES_BUCKET = os.environ["NOTES_BUCKET"]
TABLE_NAME = os.environ["TABLE_NAME"]

table = dynamodb.Table(TABLE_NAME)

# CORS headers are needed on every response, or the browser will block
# the frontend from reading the response even if the request succeeded.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "OPTIONS,POST",
}


def response(status_code, body_dict):
    """Small helper so every return statement has the same shape."""
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body_dict),
    }


def lambda_handler(event, context):
    try:
        # API Gateway proxy integration puts the raw request body here as a string.
        body = json.loads(event.get("body") or "{}")

        student_id = body.get("student_id")
        filename = body.get("filename")
        file_content_base64 = body.get("file_content_base64")

        # Basic validation - fail fast with a clear message rather than
        # letting a cryptic error happen deeper in the function.
        if not student_id or not filename or not file_content_base64:
            return response(400, {
                "error": "student_id, filename, and file_content_base64 are all required"
            })

        # Generate a unique ID for this note so multiple uploads never collide.
        note_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Decode the Base64 text back into raw file bytes.
        file_bytes = base64.b64decode(file_content_base64)

        # Store the file under a path scoped to the student, so it's easy
        # to find (and easy to lock down permissions per-student later).
        s3_key = f"notes/{student_id}/{note_id}_{filename}"

        s3_client.put_object(
            Bucket=NOTES_BUCKET,
            Key=s3_key,
            Body=file_bytes,
        )

        # Record this upload in DynamoDB using our single-table design:
        # PK groups everything by student, SK identifies this specific note.
        table.put_item(
            Item={
                "PK": f"USER#{student_id}",
                "SK": f"NOTE#{note_id}",
                "content": {
                    "filename": filename,
                    "s3Key": s3_key,
                },
                "status": "uploaded",
                "createdAt": timestamp,
            }
        )

        return response(200, {
            "message": "Upload successful",
            "note_id": note_id,
        })

    except Exception as e:
        # Never let a raw Python traceback leak back to the client.
        # Log the real error for yourself (visible in CloudWatch), return a
        # generic message to the caller.
        print(f"ERROR in upload_handler: {str(e)}")
        return response(500, {"error": "Something went wrong while uploading the file"})