FROM python:3.9

# Install Firefox and dependencies
RUN apt-get update && \
    apt-get install -y \
    firefox-esr \
    wget

# Install GeckoDriver
RUN wget https://github.com/mozilla/geckodriver/releases/download/v0.33.0/geckodriver-v0.33.0-linux64.tar.gz \
    && tar -xzf geckodriver-v0.33.0-linux64.tar.gz \
    && mv geckodriver /usr/local/bin/ \
    && rm geckodriver-v0.33.0-linux64.tar.gz

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

CMD ["gunicorn", "app:app"]
