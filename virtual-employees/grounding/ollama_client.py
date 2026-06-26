"""
Ollama Client
=============
Lightweight HTTP wrapper for the local Ollama API.

All LLM calls in this project go through this module.
To change the model or URL, edit the defaults here or
pass parameters explicitly.

Usage:
    from grounding.ollama_client import OllamaClient
    client = OllamaClient()
    response = client.generate("Summarize this code: ...")
"""

import requests
from typing import Optional

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULT_MODEL   = "qwen3"
DEFAULT_URL     = "http://localhost:11434"
DEFAULT_TIMEOUT = 60  # seconds — qwen3 can be slow on first call


class OllamaClient:
    """
    Simple client for the Ollama local LLM API.

    Parameters
    ----------
    model   : Ollama model name (default: qwen3)
    base_url: Ollama server URL (default: http://localhost:11434)
    timeout : HTTP timeout in seconds (default: 60)
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.model    = model
        self.base_url = base_url.rstrip("/")
        self.timeout  = timeout

     

    def ollama_generate(prompt):
      response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "tinyllama",   # or "phi", "mistral"
            "prompt": prompt,
            "stream": False
        }
    )

      return response.json()["response"]
    
    def is_available(self) -> bool:
        """
        Check if Ollama is running and the model is available.
        Returns True if reachable, False otherwise.
        """
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False
            models = [m["name"] for m in resp.json().get("models", [])]
            # Match exact name OR base name (e.g. "qwen3" matches "qwen3:8b")
            return any(
                m == self.model or m.split(":")[0] == self.model.split(":")[0]
                for m in models
            )
        except requests.exceptions.ConnectionError:
            return False
        except Exception:
            return False

    def generate(self, prompt: str, system: Optional[str] = None) -> str:
        """
        Send a prompt to Ollama and return the response text.

        Parameters
        ----------
        prompt  : the user prompt
        system  : optional system instruction to guide the model's behavior

        Returns the model's response as a string.
        Raises RuntimeError if Ollama is unreachable.
        """
        payload = {
            "model":  self.model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system

        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()

        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Make sure Ollama is running: run 'ollama serve' in a terminal."
            )
        except requests.exceptions.Timeout:
            raise RuntimeError(
                f"Ollama request timed out after {self.timeout}s. "
                "Try increasing the timeout or using a lighter model."
            )
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"Ollama API error: {e}")

    def summarize_file(self, file_path: str, file_content: str) -> str:
        """
        Ask the LLM to summarize a source file in 1-2 sentences.

        Parameters
        ----------
        file_path    : relative path of the file (used for context)
        file_content : the file's text content (truncated if needed)

        Returns a short summary string.
        """
        prompt = (
            f"File: {file_path}\n\n"
            f"{file_content}\n\n"
            "In 1-2 sentences, describe what this file does, "
            "its main functions or classes, and its role in the project. "
            "Be concise and technical."
        )
        system = (
            "You are a senior software engineer analyzing a codebase. "
            "Respond with only the summary — no preamble, no markdown, no bullet points."
        )
        return self.generate(prompt, system=system)

    def summarize_repo(self, file_summaries: list[dict]) -> str:
        """
        Ask the LLM to produce a global repo summary from individual file summaries.

        Parameters
        ----------
        file_summaries : list of dicts with 'path' and 'summary' keys

        Returns a 2-3 sentence description of the entire project.
        """
        summary_lines = "\n".join(
            f"- {f['path']}: {f.get('summary', 'no summary')}"
            for f in file_summaries
            if f.get("summary")
        )
        prompt = (
            f"Here are summaries of the key files in a software project:\n\n"
            f"{summary_lines}\n\n"
            "In 2-3 sentences, describe what this project does overall, "
            "its main purpose, and its key components."
        )
        system = (
            "You are a senior software engineer. "
            "Respond with only the project description — no preamble, no markdown."
        )
        return self.generate(prompt, system=system)

    def answer_question(self, question: str, context: str) -> str:
        """
        Answer a question about a repo using the provided context pack.

        Parameters
        ----------
        question : the user's question about the repo
        context  : the formatted context pack (repo summary + file summaries)

        Returns a natural language answer.
        """
        prompt = (
            f"Here is the context about a software repository:\n\n"
            f"{context}\n\n"
            f"Question: {question}\n\n"
            "Answer based only on the context provided above. "
            "Be concise and specific."
        )
        system = (
            "You are a helpful software engineer assistant with deep knowledge "
            "of the codebase described in the context. Answer questions accurately "
            "and concisely. If the answer is not in the context, say so clearly."
        )
        return self.generate(prompt, system=system)
