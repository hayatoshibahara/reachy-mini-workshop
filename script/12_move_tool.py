"""ロボットの動き（ダンス・感情）を使った Function Calling。

使い方: python script/12_move_tool.py [指示]
"""

import sys

from reachy_mini import ReachyMini

from common import (
    CAMERA_TOOL,
    LLM_MODEL,
    REACHY_MINI_SYSTEM_PROMPT,
    build_move_tool,
    get_client,
    respond_with_tools,
    run,
)

user_input = " ".join(sys.argv[1:]) or "元気な感じで踊ってみて"
client = get_client()
registry, play_move_tool = build_move_tool()


async def main(mini):
    response = client.responses.create(
        model=LLM_MODEL,
        instructions=REACHY_MINI_SYSTEM_PROMPT,
        tools=[CAMERA_TOOL, play_move_tool],
        input=user_input,
    )
    response = await respond_with_tools(client, mini, response, registry)
    print(response.output_text)


with ReachyMini(media_backend="default") as mini:
    run(main(mini))
