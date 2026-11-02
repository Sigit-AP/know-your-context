# Struct.new classes and keyword args.
Point3 = Struct.new(:x, :y, :z) do
  def volume
    x * y * z
  end
end

class Sized
  def initialize(width:, height:)
    @width = width
    @height = height
  end

  def area
    @width * @height
  end
end
