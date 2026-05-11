/**
 * ✨ Gemma 2 本地大脑调用演示脚本 ✨
 *
 * 这个脚本展示了如何在你的 TypeScript/Node.js 项目中，
 * 将刚刚安装的本地 Gemma 模型作为一个完全免费、隐私的 API 来使用。
 *
 * 运行方式 (需要安装 ts-node):
 * npx ts-node gemma_demo.ts
 */

const OLLAMA_URL = 'http://localhost:11434/api/generate';

// 我们定义一个通用的模型调用函数 API
async function askGemma(prompt: string, systemPrompt: string) {
    console.log(`\n🤖 [系统正在思考中...]`);
    try {
        const response = await fetch(OLLAMA_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                model: 'gemma2', // 我们刚刚下载的本地模型名称
                prompt: prompt,
                system: systemPrompt, // 注入“系统级提示词”来改变模型的人格
                stream: false // 这里为了演示方便我们关闭流式传输。真实产品中可以开启，实现类似 ChatGPT 一字一字吐出的效果
            })
        });

        const data = await response.json();
        return data.response;
    } catch (error) {
        console.error("❌ 调用失败，请确保上方状态栏里有 Ollama (羊驼) 图标在运行。");
        return null;
    }
}

// ================ 测试用例 =================

async function runDemo() {
    console.log("=========================================");
    console.log("   尝试启动本地大脑: Gemma 2 (9B)    ");
    console.log("=========================================\n");

    // 【场景一：铁人三项教练】
    const coachSystemPrompt = "你是一名世界顶尖的铁人三项（游泳、自行车、跑步）教练。你的回复专业、严厉但不失鼓励。你必须使用中文回复。";
    const userQuestion = "教练，我最近跑步胫骨总是疼，但下周就有个比较重要的比赛，我该怎么办？";

    console.log(`🙋‍♂️ [铁三学员]: ${userQuestion}`);
    const coachAnswer = await askGemma(userQuestion, coachSystemPrompt);
    console.log(`🚵‍♂️ [铁三教练]: ${coachAnswer}\n`);


    // 【场景二：陪伴宠物（小猫）】
    const petSystemPrompt = "你是一只傲娇但内心很依赖主人的流浪猫，刚刚被我收养。你会用符合猫咪性格的方式与我对话，经常在句末带上喵的声音。";
    const userInteraction = "今天下雨了，不能带你出去玩啦。";

    console.log(`🙋‍♂️ [主人]: ${userInteraction}`);
    const petAnswer = await askGemma(userInteraction, petSystemPrompt);
    console.log(`🐈 [宠物猫]: ${petAnswer}\n`);

    console.log("🎉 测试完成！你的大脑接入非常成功！");
}

runDemo();
