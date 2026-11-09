"""Create the chat model and the embedding model from the settings in .env.

The rest of the code only ever calls get_llm() and get_embeddings(), so swapping
Ollama for OpenAI, Anthropic or Google means changing .env, not the code.
"""

from langchain.chat_models import init_chat_model
from langchain_ollama import OllamaEmbeddings

from src import config


class NomicOllamaEmbeddings(OllamaEmbeddings):
    """nomic-embed-text was trained with task labels in front of the text.

    Stored passages get "search_document: " and questions get "search_query: ".
    """

    def embed_documents(self, texts):
        return super().embed_documents([f"search_document: {t}" for t in texts])

    def embed_query(self, text):
        return super().embed_query(f"search_query: {text}")

    async def aembed_documents(self, texts):
        return await super().aembed_documents([f"search_document: {t}" for t in texts])

    async def aembed_query(self, text):
        return await super().aembed_query(f"search_query: {text}")


def get_embeddings():
    if config.EMBED_PROVIDER == "ollama":
        cls = NomicOllamaEmbeddings if "nomic" in config.EMBED_MODEL else OllamaEmbeddings
        return cls(model=config.EMBED_MODEL, base_url=config.OLLAMA_BASE_URL)
    from langchain.embeddings import init_embeddings

    return init_embeddings(f"{config.EMBED_PROVIDER}:{config.EMBED_MODEL}")


def get_llm(**overrides):
    """Chat model for the agents. Temperature 0 and a fixed seed for repeatable runs."""
    kwargs = {"temperature": config.LLM_TEMPERATURE}
    if config.LLM_PROVIDER == "ollama":
        kwargs.update(base_url=config.OLLAMA_BASE_URL, num_ctx=config.LLM_NUM_CTX, seed=42)
    kwargs.update(overrides)
    return init_chat_model(config.LLM_MODEL, model_provider=config.LLM_PROVIDER, **kwargs)
