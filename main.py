"""deep_pro 启动入口：uvicorn main:app --reload"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
