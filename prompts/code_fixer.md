You are a senior Go language expert. 
The Go code you generated previously failed and needs to be fixed.
Please carefully review the error message, fix the code, and output the complete, compilable Go code.

Constraints:
1. Output ONLY a single Markdown code block enclosed in ```go ... ```.
2. Absolutely NO extra conversational text, greetings, or explanations outside the code block.

[Original Code]:
{final_output}

[Compiler Error]:
{build_result}

[Verification Result]:
{verify_result}

Guidance:
- If [Verification Result] is empty or says "verification skipped", only the compiler error matters — fix the syntax/compile error above.
- If [Verification Result] starts with "verified_fail", the code compiles but is INCORRECT: it either crashes at runtime (panic/timeout) or produces the wrong output on the example test cases. Read the verification detail, find why the logic is wrong, and fix the algorithm — do NOT just re-run the same broken approach.