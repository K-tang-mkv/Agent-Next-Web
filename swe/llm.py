from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from swe.config import LLMSettings, config
from typing import Optional, Literal, List
from openai.types.chat import ChatCompletionMessage
from tenacity import retry, wait_random_exponential, stop_after_attempt


class LLM(BaseModel):
    config: LLMSettings = Field(...)
    model: str = Field(...)
    api_key: str = Field(...)
    base_url: Optional[str] = Field(None)
    max_tokens: int = Field(1000)
    temperature: float = Field(0.7)
    client: Optional[AsyncOpenAI] = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, llm_config: Optional[LLMSettings] = None, **data):
        if llm_config is None:
            llm_config = config.llm

        client = AsyncOpenAI(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url
        )

        super().__init__(
            config=llm_config,
            model=llm_config.model,
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
            max_tokens=llm_config.max_tokens,
            temperature=llm_config.temperature,
            client=client,
            **data
        )

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
    )
    async def ask(
        self, prompt: str, stream: bool = True, system_prompt: str = "You are a helpful assistant."
    ) -> str:
        """
        Send a prompt to the LLM and get the response.

        Args:
            prompt (str): The prompt to send.
            stream (bool): Whether to stream the response.
            system_prompt (str): The system prompt to send.

        Returns:
            str: The generated response.
        """
        # Construct messages
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]

        if not stream:
            # For non-streaming requests
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=False,
            )
            return response["choices"][0]["message"]["content"].strip()

        # For streaming requests
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            stream=True,
        )

        collected_chunks = []
        collected_messages = []

        async for chunk in response:
            # Collect each streaming chunk
            collected_chunks.append(chunk)
            chunk_message = chunk["choices"][0].get("delta", {}).get("content", "")
            collected_messages.append(chunk_message)

            # Optionally print the chunk to the console
            print(chunk_message, end="", flush=True)

        print()  # Newline after streaming
        return "".join(collected_messages).strip()

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
    )
    async def aask_function(
            self,
            messages: List[dict],
            system_msgs: Optional[List[str]] = None,
            timeout: int = 60,
            tools: Optional[List[dict]] = None,
            tool_choice: Literal["none", "auto", "required"] = "auto",
            **kwargs
    ):
        """
        Ask LLM using functions/tools and return the response.

        Args:
            messages: List of conversation messages
            system_msgs: Optional system messages to prepend
            timeout: Request timeout in seconds
            tools: List of tools to use
            tool_choice: Tool choice strategy
            **kwargs: Additional completion arguments

        Returns:
            ChatCompletionMessage: The model's response
        """
        # Add system messages if provided
        if system_msgs:
            messages = [{"role": "system", "content": msg} for msg in system_msgs] + messages

        # Set up the completion request
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            timeout=timeout,
            **kwargs
        )

        # Return the first message
        return response.choices[0].message


async def main():
    llm = LLM()
    tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather of an location, the user shoud supply a location first",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA",
                    }
                },
                "required": ["location"]
            },
        }
    },
]
    response = await llm.aask_function(
        [{"role": "user", "content": "what is the weather today? using tool"}],
        tools=tools,
        tool_choice="auto",
    )
    print(response)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

