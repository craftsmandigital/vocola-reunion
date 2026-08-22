FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY command-service/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

# ctranslate2 trenger cuDNN/cuBLAS fra pip-pakkene (LD_LIBRARY_PATH nedenfor).
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/dist-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/dist-packages/nvidia/cudnn/lib:${LD_LIBRARY_PATH}

COPY app /app/app
COPY grammar /etc/command-grammar

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
