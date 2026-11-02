# Basic method definitions with defaults and splats.
def add(a, b)
  a + b
end

def greet(name, greeting = "hello")
  "#{greeting}, #{name}"
end

def variadic(first, *rest, **opts)
  [first, rest, opts]
end
