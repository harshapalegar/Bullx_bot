FROM python:3.9-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Create entrypoint script
RUN echo '#!/bin/bash\n\
if [ "$SERVICE_TYPE" = "webhook" ]; then\n\
    python app.py\n\
elif [ "$SERVICE_TYPE" = "bot" ]; then\n\
    python bot.py\n\
else\n\
    echo "Invalid SERVICE_TYPE"\n\
    exit 1\n\
fi' > /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
