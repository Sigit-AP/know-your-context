# Calls parking unresolved refs.
def helper(value : Int32) : Int32
  value * 2
end

def run(seed : Int32) : Int32
  total = helper(seed)
  helper(total)
end
