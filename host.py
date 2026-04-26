import asyncio
from contextlib import AsyncExitStack
import json
import os
import re
from typing import Any

import httpx

from client import MCPClient
from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

load_dotenv()


class ChatHost:
    def __init__(self):
        self.mcp_clients: list[MCPClient] = [MCPClient("./weather_USA.py"), MCPClient("./weather_Israel.py")]
        self.tool_clients: dict[str, tuple[MCPClient, str]] = {}
        self.clients_connected = False
        self.exit_stack = AsyncExitStack()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("Missing GEMINI_API_KEY. Set it in your .env file.")

        # For Netfree/SSL-intercepted environments.
        self.openai = OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            http_client=httpx.Client(verify=False),
        )

    @staticmethod
    def _extract_retry_delay_seconds(error_text: str) -> int | None:
        """Extract retry delay seconds from Gemini/OpenAI-compatible error text."""
        match = re.search(r"retryDelay\s*['\"]?\s*:\s*['\"](\d+)s['\"]", error_text)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    async def connect_mcp_clients(self):
        """Connect all configured MCP clients once."""
        if self.clients_connected:
            return

        for client in self.mcp_clients:
            if client.session is None:
                await client.connect_to_server()

        if not self.mcp_clients:
            raise RuntimeError("No MCP clients are connected")

        self.clients_connected = True

    async def get_available_tools(self) -> list[dict[str, Any]]:
        """Collect tools from all MCP clients and map them back to their owner."""
        await self.connect_mcp_clients()
        self.tool_clients = {}
        available_tools: list[dict[str, Any]] = []

        for client in self.mcp_clients:
            if client.session is None:
                print(f"Warning: MCP client {client.client_name} is not connected, skipping")
                continue

            try:
                response = await client.session.list_tools()
                for tool in response.tools:
                    exposed_name = f"{client.client_name}__{tool.name}"
                    if exposed_name in self.tool_clients:
                        raise RuntimeError(f"Duplicate tool name detected: {exposed_name}")

                    self.tool_clients[exposed_name] = (client, tool.name)
                    available_tools.append(
                        {
                            "type": "function",
                            "function": {
                                "name": exposed_name,
                                "description": f"[{client.client_name}] {tool.description}",
                                "parameters": tool.inputSchema,
                            },
                        }
                    )
            except Exception as e:
                print(f"Warning: Failed to get tools from {client.client_name}: {str(e)}")
                continue

        if not available_tools:
            raise RuntimeError("No tools available from any MCP client")

        return available_tools

    @staticmethod
    def _extract_city_from_query(query: str) -> str | None:
        """Extract a city name from common English/Hebrew weather prompts."""
        cleaned = query.strip().strip("?!. ,")
        if not cleaned:
            return None

        # Hebrew patterns like: מה מזג האוויר בתל אביב
        hebrew_patterns = [
            r"(?:מזג\s*האוויר\s*ב|מזג\s*אוויר\s*ב)([\u0590-\u05FF\s\-']+)$",
            r"(?:תחזית\s*ב|תחזית\s*ל)([\u0590-\u05FF\s\-']+)$",
            r"(?:מה\s*מזג\s*האוויר\s*ב)([\u0590-\u05FF\s\-']+)$",
        ]
        for pattern in hebrew_patterns:
            match = re.search(pattern, cleaned)
            if match:
                city = match.group(1).strip(" ?!.,")
                if 1 <= len(city) <= 40:
                    return city

        # English patterns like: weather in tel aviv
        english_patterns = [
            r"(?:weather\s+in|forecast\s+in)\s+([a-zA-Z\s\-']+)$",
            r"(?:weather\s+for|forecast\s+for)\s+([a-zA-Z\s\-']+)$",
            r"(?:what\s+is\s+the\s+weather\s+in)\s+([a-zA-Z\s\-']+)$",
        ]
        for pattern in english_patterns:
            match = re.search(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                city = match.group(1).strip(" ?!.,")
                if 1 <= len(city) <= 40:
                    return city

        # If it is a short plain phrase, assume city name directly.
        if "?" not in cleaned and len(cleaned) <= 40:
            return cleaned
        return None

    async def _try_open_city_weather_directly(self, query: str) -> str | None:
        """If user entered a city-like query, open that city weather page directly."""
        city = self._extract_city_from_query(query)
        if not city:
            return None

        await self.get_available_tools()
        direct_tool_name = "weather_Israel__open_weather_for_city"
        if direct_tool_name not in self.tool_clients:
            return None

        client, original_tool_name = self.tool_clients[direct_tool_name]
        if client.session is None:
            return None

        try:
            result = await asyncio.wait_for(
                client.session.call_tool(original_tool_name, {"city": city}),
                timeout=45,
            )
        except TimeoutError:
            return "Opening city weather took too long. Please try again in a few seconds."
        tool_output = "\n".join(
            block.text for block in result.content if hasattr(block, "text")
        )
        return tool_output or str(result.content)


    async def process_query(self, query: str) -> str:
        """Process a query using Gemini and available tools."""
        messages = [{"role": "user", "content": query}]
        available_tools = await self.get_available_tools()
        final_text = []
        rate_limit_retries = 0
        turns = 0

        while True:
            turns += 1
            if turns > 4:
                final_text.append("Stopped after 4 tool rounds to reduce API usage. Please ask a shorter query.")
                break
            try:
                response = self.openai.chat.completions.create(
                    model="gemini-2.0-flash",
                    messages=messages,
                    tools=available_tools,
                    tool_choice="auto",
                )
            except RateLimitError as e:
                error_text = str(e)
                retry_seconds = self._extract_retry_delay_seconds(error_text)
                if retry_seconds is not None and retry_seconds <= 90 and rate_limit_retries < 1:
                    rate_limit_retries += 1
                    await asyncio.sleep(retry_seconds)
                    continue

                if retry_seconds is not None:
                    return (
                        f"Gemini rate limit reached (429). "
                        f"Please wait about {retry_seconds} seconds and try again."
                    )
                return "Gemini rate limit reached (429). Please wait a minute and try again."
            except Exception as e:
                error_text = str(e)
                if "429" in error_text or "quota" in error_text.lower():
                    retry_seconds = self._extract_retry_delay_seconds(error_text)
                    if retry_seconds is not None:
                        return (
                            f"Gemini quota/rate limit reached. "
                            f"Try again in about {retry_seconds} seconds."
                        )
                    return "Gemini quota/rate limit reached. Please try again later."
                raise

            rate_limit_retries = 0
            message = response.choices[0].message

            if message.content:
                final_text.append(message.content)

            if not message.tool_calls:
                break

            assistant_tool_calls = []
            tool_messages = []

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments or "{}")

                if tool_name not in self.tool_clients:
                    raise RuntimeError(f"Unknown tool requested by model: {tool_name}")

                client, original_tool_name = self.tool_clients[tool_name]
                if client.session is None:
                    raise RuntimeError(f"MCP client {client.client_name} is not connected")

                result = await client.session.call_tool(original_tool_name, tool_args)
                final_text.append(f"[Calling tool {tool_name} with args {tool_args}]")
                tool_output = "\n".join(
                    block.text for block in result.content if hasattr(block, "text")
                )
                if not tool_output:
                    tool_output = str(result.content)

                assistant_tool_calls.append(
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments or "{}",
                        },
                    }
                )

                tool_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_output,
                    }
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": assistant_tool_calls,
                }
            )
            messages.extend(tool_messages)

        return "\n".join(final_text)
    
    async def chat_loop(self):
        """Run an interactive chat loop"""
        print("\nMCP Client Started!")
        print("Type your queries or 'quit' to exit.")
        
        while True:
            try:
                query = input("\nQuery: ").strip()
                
                if query.lower() == 'quit':
                    break

                direct_response = await self._try_open_city_weather_directly(query)
                if direct_response:
                    print("\n" + direct_response)
                    continue
                
                response = await self.process_query(query)
                print("\n" + response)
                
            except Exception as e:
                print(f"\nchat_loop Error: {str(e)}")
                
    async def cleanup(self):
        """Clean up resources"""
        for client in reversed(self.mcp_clients):
            await client.cleanup()
        await self.exit_stack.aclose()
        
        
async def main():
    host = ChatHost()
    try:
        await host.chat_loop()
    finally:
        await host.cleanup()
        
if __name__ == "__main__":
    asyncio.run(main())
