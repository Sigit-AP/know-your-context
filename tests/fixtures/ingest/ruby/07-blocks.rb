# Blocks, procs and lambdas.
def each_pair(items)
  items.each_slice(2) { |pair| yield pair }
end

def make_adder(amount)
  ->(x) { x + amount }
end

def apply_twice(fn, value)
  fn.call(fn.call(value))
end

DOUBLE = proc { |x| x * 2 }
