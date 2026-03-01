source venv/bin/activate

pip install -r requirements.txt

python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. agent.proto

sudo venv/bin/python agent_client.py
