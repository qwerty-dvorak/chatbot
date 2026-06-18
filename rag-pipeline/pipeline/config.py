import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Chat endpoint
    chat_base_url: str = field(default_factory=lambda: os.getenv("CHAT_BASE_URL", "http://localhost:9000/v1"))
    chat_api_key: str = field(default_factory=lambda: os.getenv("CHAT_API_KEY", "mock"))
    chat_model: str = field(default_factory=lambda: os.getenv("CHAT_MODEL", "openai/mock-chat"))

    # Text embedding endpoint
    embedding_base_url: str = field(default_factory=lambda: os.getenv("EMBEDDING_BASE_URL", "http://localhost:9001/v1"))
    embedding_api_key: str = field(default_factory=lambda: os.getenv("EMBEDDING_API_KEY", "mock"))
    text_embedding_model: str = field(default_factory=lambda: os.getenv("TEXT_EMBEDDING_MODEL", "nvidia/llama-embed-nemotron-8b"))
    text_embedding_dim: int = field(default_factory=lambda: int(os.getenv("TEXT_EMBEDDING_DIM", "4096")))

    # Multimodal embedding endpoint
    multimodal_embedding_base_url: str = field(default_factory=lambda: os.getenv("MULTIMODAL_EMBEDDING_BASE_URL", "http://localhost:9002"))
    multimodal_embedding_api_key: str = field(default_factory=lambda: os.getenv("MULTIMODAL_EMBEDDING_API_KEY", "mock"))
    multimodal_embedding_model: str = field(default_factory=lambda: os.getenv("MULTIMODAL_EMBEDDING_MODEL", "nvidia/nemotron-colembed-vl-8b-v2"))
    multimodal_embedding_dim: int = field(default_factory=lambda: int(os.getenv("MULTIMODAL_EMBEDDING_DIM", "4096")))

    # Reranker endpoint
    reranker_base_url: str = field(default_factory=lambda: os.getenv("RERANKER_BASE_URL", "http://localhost:9003"))
    reranker_api_key: str = field(default_factory=lambda: os.getenv("RERANKER_API_KEY", "mock"))
    reranker_model: str = field(default_factory=lambda: os.getenv("RERANKER_MODEL", "Qwen/Qwen3-VL-Reranker-2B"))

    # OCR endpoint
    ocr_base_url: str = field(default_factory=lambda: os.getenv("OCR_BASE_URL", "http://localhost:9004/v1"))
    ocr_api_key: str = field(default_factory=lambda: os.getenv("OCR_API_KEY", "mock"))
    ocr_model: str = field(default_factory=lambda: os.getenv("OCR_MODEL", "PaddlePaddle/PaddleOCR-VL-1.6"))
    ocr_pdf_dpi: int = field(default_factory=lambda: int(os.getenv("OCR_PDF_DPI", "150")))

    # Milvus
    milvus_host: str = field(default_factory=lambda: os.getenv("MILVUS_HOST", "localhost"))
    milvus_port: int = field(default_factory=lambda: int(os.getenv("MILVUS_PORT", "19530")))
    text_collection: str = field(default_factory=lambda: os.getenv("TEXT_COLLECTION", "rag_text_chunks"))
    image_collection: str = field(default_factory=lambda: os.getenv("IMAGE_COLLECTION", "rag_image_chunks"))

    # BM25
    bm25_index_path: str = field(default_factory=lambda: os.getenv("BM25_INDEX_PATH", "./data/bm25_index.pkl"))

    # Durable ingestion queue
    ingestion_data_dir: str = field(default_factory=lambda: os.getenv("INGESTION_DATA_DIR", "./data/ingestion"))
    ingestion_poll_interval: float = field(default_factory=lambda: float(os.getenv("INGESTION_POLL_INTERVAL", "0.5")))

    # Chunking
    chunk_strategy: str = field(default_factory=lambda: os.getenv("CHUNK_STRATEGY", "recursive"))
    chunk_size: int = field(default_factory=lambda: int(os.getenv("CHUNK_SIZE", "512")))
    chunk_overlap: int = field(default_factory=lambda: int(os.getenv("CHUNK_OVERLAP", "64")))
    sentence_window_size: int = field(default_factory=lambda: int(os.getenv("SENTENCE_WINDOW_SIZE", "3")))
    parent_chunk_size: int = field(default_factory=lambda: int(os.getenv("PARENT_CHUNK_SIZE", "2048")))

    # Retrieval
    retrieval_top_k: int = field(default_factory=lambda: int(os.getenv("RETRIEVAL_TOP_K", "20")))
    rerank_top_k: int = field(default_factory=lambda: int(os.getenv("RERANK_TOP_K", "5")))
    hybrid_alpha: float = field(default_factory=lambda: float(os.getenv("HYBRID_ALPHA", "0.5")))

    # Query enhancements
    query_enhancements: str = field(default_factory=lambda: os.getenv("QUERY_ENHANCEMENTS", "hyde,sub_queries,stepback"))
    hyde_n_documents: int = field(default_factory=lambda: int(os.getenv("HYDE_N_DOCUMENTS", "2")))
    sub_queries_count: int = field(default_factory=lambda: int(os.getenv("SUB_QUERIES_COUNT", "2")))
    hypothetical_questions_per_chunk: int = field(default_factory=lambda: int(os.getenv("HYPOTHETICAL_QUESTIONS_PER_CHUNK", "3")))
    hierarchical_mode: bool = field(default_factory=lambda: os.getenv("HIERARCHICAL_MODE", "true").lower() in ("1", "true", "yes"))
    summary_top_k: int = field(default_factory=lambda: int(os.getenv("SUMMARY_TOP_K", "3")))
    generate_summary: bool = field(default_factory=lambda: os.getenv("GENERATE_SUMMARY", "true").lower() in ("1", "true", "yes"))

    # PostgreSQL knowledge store — connects to the *chatbot-service* database.
    # Uses the same env var names and defaults as chatbot-service.
    postgres_db: str = field(default_factory=lambda: os.getenv("POSTGRES_DB", "chatbot"))
    postgres_user: str = field(default_factory=lambda: os.getenv("POSTGRES_USER", "chatbot"))
    postgres_password: str = field(default_factory=lambda: os.getenv("POSTGRES_PASSWORD", "chatbot"))
    postgres_host: str = field(default_factory=lambda: os.getenv("POSTGRES_HOST", "localhost"))
    postgres_port: int = field(default_factory=lambda: int(os.getenv("POSTGRES_PORT", "5433")))
    postgres_pool_size: int = field(default_factory=lambda: int(os.getenv("POSTGRES_POOL_SIZE", "5")))

    # Object store (local file-system, content-addressed)
    object_store_path: str = field(default_factory=lambda: os.getenv("OBJECT_STORE_PATH", "./data/object_store"))

    def __post_init__(self) -> None:
        # Validate numeric bounds that would cause confusing downstream errors
        if self.text_embedding_dim <= 0:
            raise ValueError(f"TEXT_EMBEDDING_DIM must be positive, got {self.text_embedding_dim}")
        if self.multimodal_embedding_dim <= 0:
            raise ValueError(f"MULTIMODAL_EMBEDDING_DIM must be positive, got {self.multimodal_embedding_dim}")
        if self.ocr_pdf_dpi <= 0:
            raise ValueError(f"OCR_PDF_DPI must be positive, got {self.ocr_pdf_dpi}")
        if self.ingestion_poll_interval <= 0:
            raise ValueError(
                f"INGESTION_POLL_INTERVAL must be positive, got {self.ingestion_poll_interval}"
            )
        if self.chunk_size <= 0:
            raise ValueError(f"CHUNK_SIZE must be positive, got {self.chunk_size}")
        if self.chunk_overlap < 0:
            raise ValueError(f"CHUNK_OVERLAP must be non-negative, got {self.chunk_overlap}")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) must be less than CHUNK_SIZE ({self.chunk_size})"
            )
        if not (0.0 <= self.hybrid_alpha <= 1.0):
            raise ValueError(f"HYBRID_ALPHA must be between 0.0 and 1.0, got {self.hybrid_alpha}")


cfg = Config()
