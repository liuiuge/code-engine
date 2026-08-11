package main

import "fmt"

// TreeNode represents a node in the binary tree.
type TreeNode struct {
	Val   int
	Left  *TreeNode
	Right *TreeNode
}

func inorderTraversal(root *TreeNode) []int {
	var result []int

	// Stack to store nodes for non-recursive traversal
	stack := make([]*TreeNode, 0)

	for root != nil || len(stack) > 0 {
		// Go as far left as possible and push nodes onto the stack
		for root != nil {
			stack = append(stack, root)
			root = root.Left
		}

		// Pop from stack if it's not empty or when we backtrack to a node with right child
		if len(stack) > 0 {
			node := stack[len(stack)-1]  // Get the top of the stack
			stack = stack[:len(stack)-1] // Remove the top

			result = append(result, node.Val)

			// Move to the right child if it exists
			if node.Right != nil {
				root = node.Right
			} else {
				root = nil
			}
		}
	}

	return result
}

func main() {
	// Construct a sample binary tree:
	//       4
	//      / \
	//     2   3

	leftNode := &TreeNode{Val: 2, Left: nil, Right: nil}
	rightNode := &TreeNode{Val: 3, Left: nil, Right: nil}

	root := &TreeNode{Val: 4}

	// Link nodes to form the tree structure
	root.Left = leftNode
	root.Right = rightNode

	fmt.Println(inorderTraversal(root))
}
