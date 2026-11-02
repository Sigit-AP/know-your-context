# Basic defs with types and defaults.
def add(a : Int32, b : Int32) : Int32
  a + b
end

def greet(name : String, greeting = "hello") : String
  "#{greeting}, #{name}"
end

def variadic(first : Int32, *rest : Int32) : Int32
  rest.sum(first)
end
