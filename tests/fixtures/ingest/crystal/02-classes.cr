# Classes with getters, initialize and class methods.
class Point
  property x : Int32
  property y : Int32

  def initialize(@x : Int32 = 0, @y : Int32 = 0)
  end

  def move(dx : Int32, dy : Int32) : self
    @x += dx
    @y += dy
    self
  end

  def origin? : Bool
    @x.zero? && @y.zero?
  end

  def self.build : self
    new
  end
end
