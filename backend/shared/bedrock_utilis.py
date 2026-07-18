"""
bedrock_utils.py
----------------
Shared helper for calling Claude via Amazon Bedrock.

NOTE ON WHY THIS CODE IS DUPLICATED INTO EACH LAMBDA:
Each Lambda function is packaged and deployed independently, so it can't
simply "import" a file from a different function's folder. The proper
fix for sharing code across many functions is a Lambda Layer - but for
a project this size (3 functions using this helper), setting up a layer
adds real complexity for very little benefit. Instead, this exact
function is copied into each Lambda that needs it. This file exists so
you have ONE place to look at and explain the logic, and one place to
update if you ever change the prompt-calling logic - just remember to
copy the change into each lambda_function.py too.
"""

import json
import boto3
import os

bedrock_client = boto3.client("bedrock-runtime")

# Set this to whichever works in your account - see the note in Module 7.
MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-haiku-4-5-20251001-v1:0"
)


def invoke_claude(prompt, max_tokens=1000):
    """
    Sends a prompt to Claude via Bedrock and returns the plain text reply.
    Uses Anthropic's "Messages API" format, which is what Bedrock expects
    for Claude models.
    """
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        
        
        
        
        
        
    }

    response = bedrock_client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(request_body),
    )

    # The response body is a stream - read() gives us the raw bytes,
    # which we decode and parse as JSON.
    response_body = json.loads(response["body"].read())

    # Claude's reply text lives inside content[0]["text"]
    return response_body["content"][0]["text"]