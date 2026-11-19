from langchain_openai import ChatOpenAI

from core.config import cfg

model = ChatOpenAI(
    api_key=cfg["llm"]["api_key"],
    base_url=cfg["llm"]["base_url"],
    model=cfg["llm"]["model"],
    streaming=True,
)
