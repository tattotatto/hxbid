# 宏曦标书 (HongXi Bidding)

AI-powered bid document generation system.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.

## Quick Start

```bash
# Copy and configure environment
cp .env.example .env

# Create virtual environment
python -m venv venv
source venv/bin/activate   # Linux / macOS
venv\Scripts\activate      # Windows

# Install dependencies
cd backend
pip install -r requirements.txt

# Run development server
uvicorn app.main:app --reload
```

## Tech Stack

- **Framework:** FastAPI (Python)
- **Database:** PostgreSQL (async via SQLAlchemy + asyncpg)
- **AI:** DeepSeek (deepseek-chat)
- **Frontend:** Vite + React (coming soon)

## Project Structure

```
backend/
  app/
    __init__.py
    config.py      # Pydantic-settings configuration
    main.py        # FastAPI application entry point
    api/           # API route modules (coming)
    models/        # SQLAlchemy models (coming)
    services/      # Business logic (coming)
  requirements.txt
.env.example
```
