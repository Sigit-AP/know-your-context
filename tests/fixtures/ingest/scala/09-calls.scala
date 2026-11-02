// Call expressions parking unresolved refs.
package demo

def helper(value: Int): Int = value * 2

def run(seed: Int): Int = {
  val total = helper(seed)
  helper(total)
}
