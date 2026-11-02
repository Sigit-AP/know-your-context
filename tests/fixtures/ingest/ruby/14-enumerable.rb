# Enumerable mixin with each contract.
class Bag
  include Enumerable

  def initialize
    @items = []
  end

  def each(&block)
    @items.each(&block)
  end

  def add(item)
    @items << item
    self
  end
end
