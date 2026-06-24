#set page(width: 420mm, height: 236mm, margin: 13mm, fill: rgb("f8fafc"))
#set text(font: "Noto Sans", size: 9.5pt, fill: rgb("172033"))
#set heading(numbering: none)
#set par(leading: 0.58em)

#let ink = rgb("172033")
#let muted = rgb("64748b")
#let line = rgb("cbd5e1")
#let blue = rgb("d9ecff")
#let blue-stroke = rgb("2563eb")
#let red = rgb("ffe2df")
#let red-stroke = rgb("e5484d")
#let green = rgb("dcfce7")
#let green-stroke = rgb("16a34a")
#let amber = rgb("fff2cc")
#let amber-stroke = rgb("d97706")
#let gray-fill = rgb("eef2f7")
#let gray-stroke = rgb("94a3b8")
#let title-block(title, subtitle: none) = {
  block(width: 100%)[
    #text(size: 20pt, weight: "bold", fill: ink)[#title]
    #if subtitle != none [
      #v(2pt)
      #text(size: 9.5pt, fill: muted)[#subtitle]
    ]
  ]
  v(7mm)
}

#let panel(title, body, fill: white, stroke: line, width: 100%, min-height: auto) = block(
  width: width,
  height: min-height,
  inset: 8pt,
  radius: 4pt,
  fill: fill,
  stroke: 0.8pt + stroke,
)[
  #text(size: 10.5pt, weight: "bold", fill: ink)[#title]
  #v(4pt)
  #text(size: 8.6pt, fill: rgb("334155"))[#body]
]

#let service(title, lines, fill: white, stroke: line) = panel(
  title,
  stack(dir: ttb, spacing: 2.5pt, ..lines.map(line => [#line])),
  fill: fill,
  stroke: stroke,
)

#let chip(text-body, fill: gray-fill, stroke: gray-stroke) = box(
  inset: (x: 6pt, y: 3pt),
  radius: 3pt,
  fill: fill,
  stroke: 0.6pt + stroke,
)[#text(size: 7.8pt, fill: rgb("334155"))[#text-body]]

#let arrow(label: none) = align(center + horizon)[
  #text(size: 18pt, fill: muted)[->]
  #if label != none [#v(-2pt)#text(size: 7.5pt, fill: muted)[#label]]
]

#let lane(label, content, fill: white, stroke: line) = block(
  width: 100%,
  inset: 7pt,
  radius: 5pt,
  fill: fill,
  stroke: 0.8pt + stroke,
)[
  #text(size: 8pt, weight: "bold", fill: muted)[#upper(label)]
  #v(5pt)
  #content
]

#let small-step(title, body, fill: white, stroke: line) = block(
  width: 100%,
  inset: 6pt,
  radius: 4pt,
  fill: fill,
  stroke: 0.7pt + stroke,
)[
  #text(size: 8.6pt, weight: "bold")[#title]
  #v(3pt)
  #text(size: 7.6pt, fill: rgb("475569"))[#body]
]

#title-block(
  [BARC System Architecture],
  subtitle: [Deployment view for chatbot-service, rag-pipeline, shared storage, and GPU model endpoints.],
)

#grid(
  columns: (31mm, 8mm, 72mm, 8mm, 92mm, 8mm, 118mm),
  rows: auto,
  gutter: 0pt,
  align: horizon,
)[
  #lane([Client], service([User], ([Browser UI], [SSE chat stream], [Document uploads]), fill: amber, stroke: amber-stroke), fill: white)
][#arrow(label: [HTTP])][
  #lane([Server B - Chatbot Service :8080], stack(dir: ttb, spacing: 5pt,
    service([Django Chat API], ([/chat over HTTP SSE], [/v1/ingest proxy], [/v1/search proxy]), fill: blue, stroke: blue-stroke),
    service([Context Builder], ([documents + memories], [prompt compaction], [tool result stitching]), fill: blue, stroke: blue-stroke),
    service([Tool Executor], ([tool calls], [stream-safe results]), fill: blue, stroke: blue-stroke),
    service([LiteLLM Client], ([OpenAI-compatible HTTP], [chat model routing]), fill: blue, stroke: blue-stroke),
  ), fill: rgb("eff6ff"), stroke: blue-stroke)
][#arrow(label: [RAG API])][
  #lane([Server A - RAG Pipeline :8093], stack(dir: ttb, spacing: 5pt,
    service([FastAPI RAG API], ([/v1/ingest], [/v1/search], [job status]), fill: red, stroke: red-stroke),
    service([Ingestion Workers], ([extract], [chunk], [summarize], [embed], [index]), fill: red, stroke: red-stroke),
    service([Search Pipeline], ([HyDE + subqueries], [dense + sparse retrieval], [RRF fusion + rerank], [parent context fetch]), fill: red, stroke: red-stroke),
    grid(columns: (1fr, 1fr), gutter: 5pt,
      service([Milvus], ([rag_text_chunks], [rag_image_chunks], [user_memories]), fill: gray-fill, stroke: gray-stroke),
      service([PostgreSQL 16], ([documents + chunks], [assets + jobs], [shared reads]), fill: gray-fill, stroke: gray-stroke),
    ),
  ), fill: rgb("fff1f0"), stroke: red-stroke)
][#arrow(label: [model HTTP])][
  #lane([GPU / RunPod Model Endpoints], stack(dir: ttb, spacing: 5pt,
    grid(columns: (1fr, 1fr), gutter: 5pt,
      service([Chat LLM], ([Gemma 4], [port 9000], [LoRA adapters]), fill: green, stroke: green-stroke),
      service([Text Embeddings], ([Llama-Embed], [port 9001], [text vectors]), fill: green, stroke: green-stroke),
    ),
    grid(columns: (1fr, 1fr, 1fr), gutter: 5pt,
      service([Multimodal Embeddings], ([Nemotron-VL], [port 9002], [image vectors]), fill: green, stroke: green-stroke),
      service([Reranker], ([Qwen3-VL], [port 9003], [final ranking]), fill: green, stroke: green-stroke),
      service([OCR], ([PaddleOCR], [port 9004], [image/PDF text]), fill: green, stroke: green-stroke),
    ),
    panel([Deployment Modes], stack(dir: ltr, spacing: 4pt,
      chip([local GPU], fill: green, stroke: green-stroke),
      chip([RunPod GPU], fill: green, stroke: green-stroke),
      chip([mock chat LLM], fill: gray-fill, stroke: gray-stroke),
    ), fill: white, stroke: line),
  ), fill: rgb("f0fdf4"), stroke: green-stroke)
]

#v(6mm)
#panel([Cross-service contracts], grid(columns: (1fr, 1fr, 1fr), gutter: 6pt,
  [#text(weight: "bold")[Chat stream]#linebreak()Browser receives incremental MessageDelta events through Django over HTTP SSE.],
  [#text(weight: "bold")[RAG delegation]#linebreak()chatbot-service delegates document ingest and search to rag-pipeline when RAG_API_ENABLED=true.],
  [#text(weight: "bold")[Shared state]#linebreak()PostgreSQL stores canonical documents and jobs; Milvus stores searchable vectors and user memories.],
), fill: white, stroke: line)

#pagebreak()
#title-block([Chat Flow Sequence], subtitle: [Request path for a user message from browser stream to model response.])

#grid(columns: (1fr, 1fr, 1fr, 1fr, 1fr, 1fr, 1fr), gutter: 5pt, align: horizon,
  small-step([1. User message], [Browser sends chat request and opens SSE response stream.], fill: amber, stroke: amber-stroke),
  arrow(label: [SSE]),
  small-step([2. Context Builder], [Resolves selected documents, memories, conversation state, and compaction.], fill: blue, stroke: blue-stroke),
  arrow(label: [optional]),
  small-step([3. RAG Search], [Calls /v1/search for relevant chunks and parent context.], fill: red, stroke: red-stroke),
  arrow(label: [prompt]),
  small-step([4. LiteLLM], [Builds OpenAI-compatible chat call for Gemma 4.], fill: blue, stroke: blue-stroke),
)
#v(7pt)
#grid(columns: (1fr, 1fr, 1fr, 1fr, 1fr), gutter: 5pt, align: horizon,
  small-step([5. Tool call loop], [Tool Executor runs requested tools and returns tool_result messages.], fill: blue, stroke: blue-stroke),
  arrow(label: [HTTP]),
  small-step([6. Chat LLM], [Streams assistant deltas from the vLLM endpoint.], fill: green, stroke: green-stroke),
  arrow(label: [SSE]),
  small-step([7. MessageDelta], [Django forwards model deltas to the browser and stores final state.], fill: blue, stroke: blue-stroke),
)
#v(9mm)
#grid(columns: (1fr, 1fr, 1fr), gutter: 7pt,
  panel([Memory lookup], [User memories live in Milvus and are pulled into the prompt when relevant.], fill: white, stroke: line),
  panel([Tool results], [Tool output is inserted back into the model conversation before streaming continues.], fill: white, stroke: line),
  panel([Compaction], [Long chats are compacted so the active prompt stays within the model context budget.], fill: white, stroke: line),
)

#pagebreak()
#title-block([Document Ingestion Pipeline], subtitle: [How text, PDF, and image inputs become searchable records and vectors.])

#grid(columns: (1fr, 1fr), gutter: 8pt,
  lane([Text documents], stack(dir: ttb, spacing: 5pt,
    small-step([Upload], [POST /v1/ingest creates a queued ingestion job.], fill: amber, stroke: amber-stroke),
    small-step([Extract + normalize], [Raw content is persisted, parsed, and normalized for chunking.], fill: red, stroke: red-stroke),
    small-step([Chunk + enrich], [Chunks receive summaries and hypothetical questions for index-time query expansion.], fill: red, stroke: red-stroke),
    small-step([Embed + index], [Text embeddings are written to Milvus; metadata is committed to PostgreSQL.], fill: red, stroke: red-stroke),
  ), fill: rgb("fff1f0"), stroke: red-stroke),
  lane([Images and PDFs], stack(dir: ttb, spacing: 5pt,
    small-step([Upload], [Source image or PDF page is stored as an asset.], fill: amber, stroke: amber-stroke),
    small-step([Preprocess], [EXIF normalization, page rendering, and optional image splitting.], fill: red, stroke: red-stroke),
    small-step([Dual-track extraction], [PaddleOCR produces text while multimodal embeddings preserve visual search.], fill: red, stroke: red-stroke),
    small-step([Index both tracks], [OCR text reuses the text pipeline; image vectors go to rag_image_chunks.], fill: red, stroke: red-stroke),
  ), fill: rgb("fff1f0"), stroke: red-stroke),
)
#v(8mm)
#panel([Storage outputs], grid(columns: (1fr, 1fr, 1fr), gutter: 6pt,
  [#text(weight: "bold")[PostgreSQL]#linebreak()documents, chunks, jobs, assets, source paths],
  [#text(weight: "bold")[Milvus text collection]#linebreak()source chunks plus separate hypothetical-question vectors],
  [#text(weight: "bold")[Milvus image collection]#linebreak()derived image/page vectors with parent links],
), fill: white, stroke: line)

#pagebreak()
#title-block([Retrieval / Search Pipeline], subtitle: [Query enhancement, retrieval, fusion, reranking, and parent-context assembly.])

#grid(columns: (1fr, 1fr, 1fr, 1fr, 1fr, 1fr, 1fr, 1fr, 1fr), gutter: 4pt, align: horizon,
  small-step([Query], [User search or chat retrieval request.], fill: blue, stroke: blue-stroke),
  arrow(),
  small-step([Enhance], [HyDE, subqueries, stepback, and filters.], fill: red, stroke: red-stroke),
  arrow(),
  small-step([Embed], [Vectorize enhanced queries.], fill: red, stroke: red-stroke),
  arrow(),
  small-step([Retrieve], [Dense Milvus search plus sparse BM25.], fill: red, stroke: red-stroke),
  arrow(),
  small-step([Return], [Ranked results with source metadata.], fill: blue, stroke: blue-stroke),
)
#v(7mm)
#grid(columns: (1fr, 1fr, 1fr, 1fr), gutter: 6pt,
  panel([Fusion], [RRF merges dense and sparse candidate sets, removes duplicates, and preserves useful diversity.], fill: white, stroke: line),
  panel([Reranking], [Qwen3-VL reranker scores the fused candidate set before final selection.], fill: white, stroke: line),
  panel([Parent resolution], [Hypothetical-question hits are replaced with their parent document chunks at search time.], fill: white, stroke: line),
  panel([Hierarchical mode], [Optional stage 1 searches source summaries; stage 2 searches chunks filtered to top source paths.], fill: white, stroke: line),
)
