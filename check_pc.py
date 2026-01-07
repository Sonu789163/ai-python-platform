
from pinecone import Pinecone
from app.core.config import settings

def check_stats():
    pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    index = pc.Index(host=settings.PINECONE_DRHP_HOST)
    stats = index.describe_index_stats()
    print(stats)

if __name__ == "__main__":
    check_stats()
