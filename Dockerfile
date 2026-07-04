# Use stable slim Python image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Prevent Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies required for FAISS, PDFs, etc.
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    curl \
    git \
    poppler-utils \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip first
RUN pip install --upgrade pip

# Install lightweight CPU version of torch FIRST (more stable download)
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# Copy requirements file
COPY requirements.txt .

# Install remaining dependencies
RUN pip install --no-cache-dir --retries 10 --timeout 1000 -r requirements.txt

# Copy project files
COPY . .

# Expose Streamlit default port
EXPOSE 8501

# Run Streamlit app
CMD ["streamlit", "run", "Ticket.py", "--server.port=8501", "--server.address=0.0.0.0"]