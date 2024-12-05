FROM python:3.9

# Install Firefox and GeckoDriver
#RUN apt-get update && apt-get install -y firefox-esr
# Update these lines in your Dockerfile
RUN apt-get update && apt-get install -y firefox-esr=102.* geckodriver
RUN wget https://github.com/mozilla/geckodriver/releases/download/v0.33.0/geckodriver-v0.33.0-linux64.tar.gz \
    && tar -xzf geckodriver-v0.33.0-linux64.tar.gz \
    && mv geckodriver /usr/local/bin/

WORKDIR /app
COPY . .
RUN pip install -r requirements.txt

CMD ["gunicorn", "app:app"]
