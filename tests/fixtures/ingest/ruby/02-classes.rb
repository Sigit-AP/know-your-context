# Classes with instance/class methods and accessors.
class Point
  attr_accessor :x, :y

  def initialize(x = 0, y = 0)
    @x = x
    @y = y
  end

  def move(dx, dy)
    @x += dx
    @y += dy
    self
  end

  def origin?
    @x.zero? && @y.zero?
  end

  def self.build
    new
  end
end
