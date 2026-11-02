# Blocks, procs and yield.
def each_pair(items : Array(Int32), & : Int32, Int32 ->)
  items.each_slice(2) { |pair| yield pair[0], pair[1] }
end

def make_adder(amount : Int32) : Int32 -> Int32
  ->(x : Int32) { x + amount }
end

def apply_twice(fn : Int32 -> Int32, value : Int32) : Int32
  fn.call(fn.call(value))
end
