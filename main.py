from workflow import app

if __name__ == "__main__":
    print(">>> start workflow...")
    result = app.invoke({"input_question": "请用 Golang 写一段非递归二叉树中序遍历"})
    
    print("\n--- final output ---")
    if result.get("category") == "coding":
        print(f"save code to: {result.get('code_path')}")
        print(f"compile check result:\n{result.get('build_result')}")
    else:
        print(result.get("final_output"))
