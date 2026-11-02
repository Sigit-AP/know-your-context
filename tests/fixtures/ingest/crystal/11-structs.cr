# Structs (value types) and enums.
struct Size
  getter width : Int32
  getter height : Int32

  def initialize(@width : Int32, @height : Int32)
  end

  def area : Int32
    width * height
  end
end

enum Level
  Low  = 1
  High = 2
end
