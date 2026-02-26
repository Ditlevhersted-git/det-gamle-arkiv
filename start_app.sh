#!/bin/bash

# 🔑 Sæt OpenAI key eksplicit (løser launcher miljø problem)
export OPENAI_API_KEY="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"

if ! curl -s http://localhost:5000 >/dev/null; then
    cd ~/pdf-sog

    source .venv/bin/activate

    nohup .venv/bin/python app/web.py >/dev/null 2>&1 &

    sleep 2
fi

cmd.exe /c start http://localhost:5000

