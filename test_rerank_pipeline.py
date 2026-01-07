
import asyncio
import os
from app.services.summarization.pipeline import SummaryPipeline
from app.core.config import settings

async def main():
    # Ensure keys are set in environment for this test session
    # (Assuming user has them in .env which we load via pydantic-settings)
    
    pipeline = SummaryPipeline()
    namespace = "NON_EXISTENT_NAME.pdf"
    
    print(f"--- Starting Test Job for {namespace} ---")
    try:
        # We only run the first stage or retrieval to see if rerank works
        # to avoid spending too much on OpenAI
        queries = ["Who are the main investors and what is the share capital history?"]
        print(f"Testing retrieval with queries: {queries}")
        
        # We manually call _retrieve_context
        context = await pipeline._retrieve_context(
            queries=queries,
            namespace=namespace,
            index_name=settings.PINECONE_RHP_INDEX,
            host=settings.PINECONE_RHP_HOST
        )
        
        print("\n--- Retrieval Successful ---")
        print(f"Context length: {len(context)} characters")
        print("First 500 chars of context:\n")
        print(context[:500])
        print("\n--- Reranking confirmed if context contains relevant data ---")

    except Exception as e:
        print(f"Error during test: {e}")

if __name__ == "__main__":
    asyncio.run(main())
