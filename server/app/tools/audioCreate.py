# ## 音频生成
# import asyncio
# import edge_tts

# async def main():
#     # 1. 配置参数
#     text = "你好，我叫晓晨，欢迎使用代码调用 TTS 音频生成。"
#     voice = "zh-CN-XiaochenNeural"  # 晓晨的音色
#     output_file = "test_output.mp3"

#     # 2. 创建 Communicate 对象
#     # 可以在这里控制语速 (rate="+10%")、音量 (volume="+0%") 和音调 (pitch="+0Hz")
#     communicate = edge_tts.Communicate(text, voice, rate="+0%")

#     # 3. 保存音频
#     print(f"正在生成音频，请稍候...")
#     await communicate.save(output_file)
#     print(f"✅ 生成成功！文件已保存为：{output_file}")

# if __name__ == "__main__":
#     # 运行异步主函数
#     asyncio.run(main())