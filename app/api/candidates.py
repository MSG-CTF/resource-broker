from fastapi import APIRouter


router = APIRouter(prefix="/v1/candidates", tags=["candidates"])

# POST /v1/candidates/query will be added after the request and response
# Pydantic schemas are implemented.
