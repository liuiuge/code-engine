from logger import logger
from workflow import app


if __name__ == "__main__":
    logger.info(">>> start workflow...")
    input_question = """
使用Golang 完成题目
## 题目描述

序列化是将一个数据结构或者对象转换为连续的比特位的操作，进而可以将转换后的数据存储在一个文件或内存缓冲区中，同时也可以通过网络传输到另一个计算机环境，采取相反方式重构得到原数据。

请设计一个算法来实现二叉树的序列化与反序列化。这里不限定你的序列化/反序列化算法执行逻辑，你只需要确保一个二叉树可以被序列化为一个字符串并且将这个字符串反序列化为原始的树结构。

**示例：**

```text
输入：root = [1,2,3,null,null,4,5]
输出：[1,2,3,null,null,4,5]

```
    """
    logger.info(f"\n[system log] input question:\n{input_question}")
    result = app.invoke({"input_question": input_question})
    
    logger.info("\n--- final output ---")
    if result.get("category") == "coding":
        logger.info (f"save code to: {result.get('code_path')}")
        logger.info(f"compile check result:\n{result.get('build_result')}")
    else:
        logger.info(result.get("final_output"))
