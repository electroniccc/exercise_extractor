#!/bin/bash

CONTAINER_NAME="paddleocr_vllm"
IMAGE_NAME="ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-genai-vllm-server:latest-nvidia-gpu"

echo "Checking if container exists..."

EXISTING=$(docker ps -a --filter "name=${CONTAINER_NAME}" --format "{{.Status}}")

if [ -z "$EXISTING" ]; then
    echo "Container does not exist. Creating and starting..."

    docker run \
        -d \
        --gpus all \
        --name ${CONTAINER_NAME} \
        -p 8080:8080 \
        -e PADDLE_HUB_HOME=/mnt/d/ai_models/paddle \
        -e PADDLE_PDX_CACHE_HOME=/mnt/d/ai_models/paddle \
        -v /mnt/c/Users/nanis/Documents/:/mnt/c/Users/nanis/Documents/ \
        -v /mnt/d/ai_models/:/mnt/d/ai_models/ \
        -v /home/nan/Projects/paddleocr_demo:/home/nan/Projects/paddleocr_demo \
        ${IMAGE_NAME} \
        paddleocr genai_server --model_name PaddleOCR-VL-1.6-0.9B --host 0.0.0.0 --port 8080 --backend vllm \
        --backend_config /home/nan/Projects/paddleocr_demo/vllm_config.yaml

else
    RUNNING=$(docker ps --filter "name=${CONTAINER_NAME}" --format "{{.Status}}")

    if [ -z "$RUNNING" ]; then
        echo "Container exists but is not running. Starting..."
        docker start ${CONTAINER_NAME}
    else
        echo "Container is already running."
    fi
fi

echo "Waiting for service to become available at http://127.0.0.1:8080/v1/models ..."
echo "Checking every 10 seconds..."

while true; do
    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/v1/models)

    if [ "$RESPONSE" -eq 200 ]; then
        echo "Service is available!"
        echo "Fetching model list..."
        curl http://127.0.0.1:8080/v1/models
        echo "Script finished."
        exit 0
    else
        echo "Service not ready yet (HTTP $RESPONSE). Retrying in 10 seconds..."
        sleep 10
    fi
done
