# Fibers and channels.
def produce(out : Channel(Int32), n : Int32) : Nil
  n.times { |i| out.send(i) }
  out.close
end

def consume(input : Channel(Int32)) : Int32
  total = 0
  while value = input.receive?
    total += value
  end
  total
end

def fanout(n : Int32) : Int32
  ch = Channel(Int32).new
  spawn produce(ch, n)
  consume(ch)
end
