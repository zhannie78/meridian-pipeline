FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt "pandas==2.3.3" "numpy==2.3.5"
COPY app.py ask.py snapshots.json ./
COPY meridian meridian
COPY web/index.html web/index.html
COPY Q1 Q1
COPY Q2 Q2
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8080", "--no-browser"]
