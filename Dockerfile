FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# The example key is a local-only placeholder; mount a real settings.toml to override.
RUN [ -f config/settings.toml ] || cp config/settings.example.toml config/settings.toml

CMD ["python", "-m", "src.compliance.evaluate", "--no-persist"]
