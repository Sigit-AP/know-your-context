# Calls parking unresolved refs (receiver-less and receiver forms).
def helper(value)
  value * 2
end

def run(seed)
  total = helper(seed)
  puts total
  helper(total)
end
