"""Wrapper around OpenAI APIs."""
import logging
import sys
from typing import (
    Any,
    Dict,
    List,
    Optional,
)
from grpclib.client import Channel

from langchain.llms.base import BaseLLM
from .llm_rpc.api import RemoteLLMStub, GenerateRequest, GenerateReply, GenerateReplyGeneration, GenerateReplyGenerationList, LLMTypeRequest, LLMTypeReply
from .schema import Generation, LLMResult
from .llm_class import ClientLLM

import asyncio
import nest_asyncio
nest_asyncio.apply()

import grpclib

logger = logging.getLogger(__name__)

def pack_generation_list(generations: List[Generation]) -> GenerateReplyGenerationList:
    generations = [GenerateReplyGeneration(text=g.text, generation_info=g.generation_info) for g in generations]
    return GenerateReplyGenerationList(generations=generations)

def unpack_generation_list(generations: GenerateReplyGenerationList) -> List[Generation]:
    return [Generation(text=g.text, generation_info=g.generation_info) for g in generations.generations]

class ClientLangchain(BaseLLM):
    """
    Remote LLM. Uses a GRPC server to generate text.
    """
    client: ClientLLM
    
    def __init__(self, client: ClientLLM):
        super().__init__(client=client)

    def _generate(
        self, prompts: List[str], stop: Optional[List[str]] = None
    ) -> LLMResult:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.client.async_generate_text(prompts, stop))

    async def _agenerate(
        self, prompts: List[str], stop: Optional[List[str]] = None
    ) -> LLMResult:
        """Generate text using the remote llm."""
        result = await self.client.async_generate_text(prompts=prompts, stop=stop)
        return LLMResult(generations=[unpack_generation_list(g) for g in result.generations])

    @property
    def _llm_type(self) -> str:
        loop = asyncio.get_event_loop()
        remote_type = loop.run_until_complete(self.client.get_llm_type())
        return f"remote:{remote_type.llm_type}"
    
    def save(self, file_path: Any) -> None:
        raise NotImplementedError("Cannot save remote LLMs.")
    

class ServiceLangchain(): 
    llm: BaseLLM

    def __init__(self, llm: BaseLLM):
        self.llm = llm

    async def Generate(self, stream: "grpclib.server.Stream[GenerateRequest, GenerateReply]") -> None:
        request = await stream.recv_message()
        print(request.prompts)
        try: 
            generations = await self.llm._agenerate(request.prompts, request.stop)
        except NotImplementedError:
            generations = self.llm._generate(request.prompts, request.stop)
        print(f"Generated {len(generations.generations)} generations.")
        print("generations",generations)
        generations = [pack_generation_list(g) for g in generations.generations]
        reply = GenerateReply(generations=generations)
        print("reply", reply)
        await stream.send_message(reply)

    async def GetLlmType(self, stream: "grpclib.server.Stream[LLMTypeRequest, LLMTypeReply]") -> None:
        request = await stream.recv_message()
        msg = LLMTypeReply(llm_type=self.llm._llm_type)
        await stream.send_message(msg)

    def __mapping__(self) -> Dict[str, "grpclib.const.Handler"]:
        return {
            "/llm_rpc.api.RemoteLLM/Generate": grpclib.const.Handler(
                self.Generate,
                grpclib.const.Cardinality.UNARY_UNARY,
                GenerateRequest,
                GenerateReply,
            ),
            "/llm_rpc.api.RemoteLLM/GetLlmType": grpclib.const.Handler(
                self.GetLlmType,
                grpclib.const.Cardinality.UNARY_UNARY,
                LLMTypeRequest,
                LLMTypeReply,
            )
        }