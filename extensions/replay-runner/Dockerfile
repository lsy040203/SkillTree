FROM python:3.11-slim
COPY adapters /runner/adapters
COPY runner.py /runner/runner.py
WORKDIR /runner
USER 65532:65532
ENTRYPOINT ["python", "runner.py"]
