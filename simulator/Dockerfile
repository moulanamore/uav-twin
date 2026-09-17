FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY uav_sim.py .

CMD ["python", "-u", "uav_sim.py"]
