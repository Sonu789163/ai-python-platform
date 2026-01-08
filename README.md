# AI Python Platform 🚀

Production-ready Python AI execution layer that replaces n8n workflows. This platform is designed to be called by an existing Node.js backend and runs scalable AI pipelines for documents, news, and summaries.

## 📋 Overview

The AI Python Platform is a FastAPI-based microservice that processes AI workloads asynchronously using Celery workers. It supports multiple environments (sandbox, dev, prod) and is ready for deployment on Azure Container Apps.

### Key Features

- ✅ **FastAPI** - High-performance async API
- ✅ **Celery** - Distributed task queue with retry logic
- ✅ **Redis** - Message broker and result backend
- ✅ **MongoDB** - Document storage
- ✅ **Structured Logging** - JSON logs with job tracking
- ✅ **Environment-based Config** - Sandbox, dev, and prod support
- ✅ **Docker-ready** - Production containers included
- ✅ **Azure-ready** - Deployable to Azure Container Apps

## 🏗️ Architecture

```text
│  Node.js Backend│
└────────┬────────┘
         │ HTTP POST /jobs/*
         ▼
┌─────────────────┐       ┌────────────────────────┐
│   FastAPI API   │ ◄────►│ Ingestion Layer (Sync) │
└────────┬────────┘       └───────────┬────────────┘
         │                            │
         │ Enqueues task (Async)      ▼
         ▼                    ┌──────────────┐
┌─────────────────┐           │   Pinecone   │
│  Redis (Broker) │           │ (Vector DB)  │
└────────┬────────┘           └──────────────┘
         │
         ▼
┌─────────────────┐       ┌──────────────┐
│  Celery Workers  │──────►│   MongoDB    │
│ (News/Summary)  │       │   (Storage)  │
└─────────────────┘       └──────────────┘
```

### Pipeline Flow

1. **Ingestion Layer (Synchronous)**:
   - Node.js backend sends a PDF URL to `/jobs/document`.
   - FastAPI downloads, cleans, chunks, and generates embeddings from the document.
   - Text chunks and metadata are upserted into **Pinecone** immediately.
   - FastAPI returns a `200 OK` success response with ingestion results.

2. **Analysis Layer (Asynchronous)**:
   - Node.js backend sends a request to `/jobs/summary` or `/jobs/news`.
   - FastAPI enqueues a task in **Redis** and returns a `job_id` (HTTP 202).
   - **Celery workers** process the task in the background.
   - Node.js backend polls `/jobs/{job_id}` for completion.

## 📁 Project Structure

```
ai-python-platform/
├── app/
│   ├── main.py                  # FastAPI entrypoint
│   │
│   ├── api/
│   │   └── jobs.py               # Job intake endpoints
│   │
│   ├── workers/
│   │   ├── celery_app.py         # Celery configuration
│   │   └── document_pipeline.py  # AI pipeline tasks
│   │
│   ├── services/
│   │   ├── extraction.py         # Text extraction
│   │   ├── chunking.py           # Text chunking
│   │   └── embedding.py          # Vector embeddings
│   │
│   ├── core/
│   │   ├── config.py             # Environment config
│   │   └── logging.py            # Structured logging
│   │
│   └── db/
│       └── mongo.py              # MongoDB connection
│
├── docker/
│   ├── api.Dockerfile            # API container
│   └── worker.Dockerfile         # Worker container
│
├── requirements.txt
├── .env.example
└── README.md
```

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Redis
- MongoDB
- (Optional) Docker

### Local Setup

1. **Clone and navigate to project**
   ```bash
   cd ai-python-platform
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

5. **Start Redis** (if not running)
   ```bash
   redis-server
   ```

6. **Start MongoDB** (if not running)
   ```bash
   mongod
   ```

### Running the Application

#### Option 1: Local Development

**Terminal 1 - Start API**
```bash
python3 -m app.main
# API available at http://localhost:8000
```

**Terminal 2 - Start Celery Worker**
```bash
celery -A app.workers.celery_app worker --loglevel=info
```

#### Option 2: Docker

**Build images**
```bash
docker build -f docker/api.Dockerfile -t ai-platform-api .
docker build -f docker/worker.Dockerfile -t ai-platform-worker .
```

**Run with docker-compose** (create docker-compose.yml first)
```bash
docker-compose up
```

## 🔌 API Endpoints

### Health Check
```http
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "environment": "sandbox",
  "version": "1.0.0"
}
```

### Submit Document Job
```http
POST /jobs/document
Content-Type: application/json

{
  "file_url": "https://example.com/document.pdf",
  "file_type": "pdf",
  "metadata": {
    "source": "user_upload"
  }
}
```

**Response (HTTP 202):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "accepted",
  "message": "Document processing job enqueued successfully"
}
```

### Submit News Job
```http
POST /jobs/news
Content-Type: application/json

{
  "article_url": "https://example.com/article",
  "metadata": {}
}
```

### Submit Summary Job
```http
POST /jobs/summary
Content-Type: application/json

{
  "text": "Long text to summarize...",
  "summary_type": "brief"
}
```

### Check Job Status
```http
GET /jobs/{job_id}
```

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "state": "SUCCESS",
  "result": {
    "chunk_count": 5,
    "char_count": 1234,
    "execution_time": 2.5
  }
}
```

## 🔧 Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `APP_ENV` | Environment (sandbox/dev/prod) | `sandbox` |
| `DEBUG` | Debug mode | `false` |
| `API_PORT` | API port | `8000` |
| `REDIS_HOST` | Redis host | `localhost` |
| `REDIS_PORT` | Redis port | `6379` |
| `MONGO_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `LOG_LEVEL` | Logging level | `INFO` |

See `.env.example` for complete configuration.

## 📊 Logging

All logs are structured JSON with the following fields:

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "info",
  "event": "job_start",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "pipeline": "document_pipeline",
  "environment": "prod",
  "execution_time": 2.5
}
```

## 🐳 Docker Deployment

### Build for Production
```bash
docker build -f docker/api.Dockerfile -t ai-platform-api:latest .
docker build -f docker/worker.Dockerfile -t ai-platform-worker:latest .
```

### Push to Registry
```bash
docker tag ai-platform-api:latest <registry>/ai-platform-api:latest
docker push <registry>/ai-platform-api:latest

docker tag ai-platform-worker:latest <registry>/ai-platform-worker:latest
docker push <registry>/ai-platform-worker:latest
```

## ☁️ Azure Container Apps Deployment

1. **Create Azure resources**
   - Container Apps Environment
   - Redis Cache
   - CosmosDB (MongoDB API) or MongoDB Atlas

2. **Configure environment variables** in Azure Container Apps

3. **Deploy API container**
   ```bash
   az containerapp create \
     --name ai-platform-api \
     --resource-group <rg> \
     --environment <env> \
     --image <registry>/ai-platform-api:latest \
     --target-port 8000 \
     --ingress external
   ```

4. **Deploy Worker container**
   ```bash
   az containerapp create \
     --name ai-platform-worker \
     --resource-group <rg> \
     --environment <env> \
     --image <registry>/ai-platform-worker:latest \
     --ingress internal
   ```

## 🔄 Celery Worker Management

### Start Worker
```bash
celery -A app.workers.celery_app worker --loglevel=info
```

### Monitor Tasks
```bash
celery -A app.workers.celery_app events
```

### Purge Queue (Development only)
```bash
celery -A app.workers.celery_app purge
```

## 🧪 Testing

From your Node.js backend:

```javascript
const response = await fetch('http://localhost:8000/jobs/document', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    file_url: 'https://example.com/doc.pdf',
    file_type: 'pdf'
  })
});

const { job_id } = await response.json();

// Poll for result
const statusResponse = await fetch(`http://localhost:8000/jobs/${job_id}`);
const status = await statusResponse.json();
```

## 📝 Development Workflow

1. **Make code changes** in `app/`
2. **Test locally** with hot reload (DEBUG=true)
3. **Commit changes** to Git
4. **Build Docker images** for deployment
5. **Deploy to environment** (sandbox → dev → prod)

## 🛠️ Troubleshooting

### Workers not processing tasks
- Check Redis connection
- Verify Celery broker URL
- Check worker logs

### API not responding
- Check FastAPI logs
- Verify port 8000 is not in use
- Check MongoDB connection

### MongoDB connection failed
- Ensure MongoDB is running
- Check `MONGO_URI` configuration
- Verify network connectivity

## 📚 Next Steps

- [ ] Implement actual AI models (embeddings, summarization)
- [ ] Add authentication/API keys for Node.js backend
- [ ] Set up monitoring (Prometheus, Grafana)
- [ ] Add rate limiting
- [ ] Implement result webhooks
- [ ] Add comprehensive tests

## 📄 License

Proprietary - Internal Use Only

---

**Built with ❤️ for scalable AI workloads**