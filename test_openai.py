import os
from openai import OpenAI

print("API key sat:", bool(os.getenv("OPENAI_API_KEY")))

client = OpenAI()

resp = client.responses.create(
    model="gpt-5",
    input="Svar kun OK"
)

print(resp.output_text)
