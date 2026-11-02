# Inheritance, super and mixins.
class Animal
  def speak
    "..."
  end
end

class Dog < Animal
  def speak
    "#{super} woof"
  end
end

module Walkable
  def walk
    "walking"
  end
end

class Puppy < Dog
  include Walkable
end
