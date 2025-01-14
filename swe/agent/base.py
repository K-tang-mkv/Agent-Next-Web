from pydantic import BaseModel, model_validator, Field
from abc import ABC, abstractmethod

from swe.action.action import Action
from swe.llm import LLM
from swe.schema import Memory, AgentState, Message

from typing import Dict, List, Optional, Type, Any, Callable, AsyncGenerator
from contextlib import asynccontextmanager
import logging
import asyncio


class BaseAgent(BaseModel, ABC):
    """Abstract base agent class for managing agent state and execution"""
    name: str
    description: Optional[str] = None

    system_prompt: Optional[str] = None
    next_step_prompt: Optional[str] = None

    tools: List[str] = Field(default_factory=list)
    tool_execution_map: Dict[str, callable] = Field(default_factory=dict)
    special_tool_commands: List[str] = Field(default_factory=lambda: ["finish"])

    max_react_loop: int = 30
    commands: List[dict] = Field(default_factory=list)

    llm: Optional[Any] = None
    memory: Memory = Field(default_factory=Memory)
    state: AgentState = AgentState.IDLE

    actions: List[Type["Action"]] = Field(default_factory=list)
    available_tools: Dict[str, callable] = Field(default_factory=dict)

    max_steps: int = 10
    current_step: int = 0

    logger: logging.Logger = Field(default_factory=lambda: logging.getLogger(__name__))

    # Memory management settings
    max_memory_messages: int = 100
    memory_summary_threshold: int = 50
    auto_summarize: bool = True

    class Config:
        arbitrary_types_allowed = True

    @model_validator(mode='after')
    def initialize_agent(self) -> "BaseAgent":
        """Initialize agent with default settings"""
        if self.llm is None:
            self.llm = LLM()
        return self

    @asynccontextmanager
    async def state_context(self, new_state: AgentState):
        """Context manager for handling agent state transitions"""
        previous_state = self.state
        self.state = new_state
        try:
            yield
        except Exception as e:
            self.state = AgentState.ERROR
            self.logger.error(f"Error in state {new_state}: {str(e)}")
            raise
        finally:
            self.state = previous_state

    @abstractmethod
    async def think(self) -> bool:
        """Process current state and decide next action"""
        pass

    @abstractmethod
    async def act(self) -> str:
        """Execute decided actions"""
        pass

    async def _summarize_memory(self) -> None:
        """Summarize conversation history"""
        try:
            if not self.memory.messages:
                return

            summary_prompt = "Please summarize the key points of this conversation:"
            messages_text = "\n".join(
                f"{m['role']}: {m['content']}"
                for m in self.memory.messages
            )

            summary = await self.llm.aask(
                f"{summary_prompt}\n\n{messages_text}"
            )

            self.memory.messages = [
                {"role": "system", "content": f"Previous conversation summary: {summary}"}
            ]
            self.logger.info("Memory summarized successfully")

        except Exception as e:
            self.logger.error(f"Error summarizing memory: {str(e)}")

    async def reset(self, clear_memory: bool = True) -> None:
        """Reset agent state"""
        self.state = AgentState.IDLE
        self.current_step = 0

        if clear_memory:
            self.memory = Memory()

        self.logger.info(f"Agent reset (clear_memory={clear_memory})")

    def update_memory(self, role: str, content: str, **kwargs) -> None:
        """Update memory with new message"""
        if role == "user":
            msg = Message.user_message(content)
        elif role == "system":
            msg = Message.system_message(content)
        elif role == "assistant":
            msg = Message.assistant_message(content)
        elif role == "tool":
            msg = Message.tool_message(content, **kwargs)
        else:
            raise ValueError(f"Unsupported message role: {role}")

        self.memory.add_message(msg)

    async def run(
            self,
            request: Optional[str] = None,
            max_steps: Optional[int] = None,
            raise_on_error: bool = True,
            reset_before_run: bool = True
    ) -> str:
        """Main execution loop"""
        if reset_before_run:
            await self.reset()

        if request:
            self.update_memory("user", request)

        steps_limit = max_steps or self.max_steps
        results = []

        async with self.state_context(AgentState.RUNNING):
            while self.current_step < steps_limit:
                self.current_step += 1

                try:
                    # Think phase
                    should_act = await self.think()
                    if not should_act:
                        results.append("Thinking complete - no action needed")
                        break

                    # Act phase
                    result = await self.act()
                    step_result = f"Step {self.current_step}: {result}"
                    results.append(step_result)

                    if self.state == AgentState.FINISHED:
                        break

                except Exception as e:
                    error_msg = f"Error in step {self.current_step}: {str(e)}"
                    self.logger.error(error_msg)
                    self.update_memory("assistant", f"Error: {error_msg}")

                    if raise_on_error:
                        raise
                    results.append(error_msg)
                    break

                await asyncio.sleep(0)

            if self.current_step >= steps_limit:
                results.append(f"Reached maximum steps limit ({steps_limit})")

        return "\n".join(results)
