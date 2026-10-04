"""カメラ画像を使った Function Calling。

使い方: python script/11_camera_tool.py [質問]
"""

import sys

from reachy_mini import ReachyMini

from common import (
    CAMERA_TOOL,
    LLM_MODEL,
    REACHY_MINI_SYSTEM_PROMPT,
    get_client,
    respond_with_tools,
    run,
)

user_input = " ".join(sys.argv[1:]) or "今カメラに何が見えるか教えて"
client = get_client()


async def main(mini):
    response = client.responses.create(
        model=LLM_MODEL,
        instructions=REACHY_MINI_SYSTEM_PROMPT,
        tools=[CAMERA_TOOL],
        input=user_input,
    )
    if any(item.type == "function_call" for item in response.output):
        print("カメラ撮影の呼び出しが検出されました。画像を取得して回答に反映します...")
    response = await respond_with_tools(client, mini, response)
    print(response.output_text)


with ReachyMini(media_backend="default") as mini:
    run(main(mini))
