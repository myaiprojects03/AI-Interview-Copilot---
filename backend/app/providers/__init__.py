def load_providers() -> None:
    """Import concrete providers so they register themselves with the registry."""
    from app.providers import assemblyai_stt, fastembed_embed, groq_llm, pinecone_store  # noqa: F401
