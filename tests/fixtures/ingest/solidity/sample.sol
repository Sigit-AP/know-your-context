pragma solidity ^0.8.0;

import "./Token.sol";

contract Greeter is Ownable {
    string name;

    function greet(string memory who) public returns (string memory) {
        return helper(who);
    }

    function helper(string memory x) internal returns (string memory) {
        return x;
    }
}

interface IGreeter {
    function greet(string memory who) external returns (string memory);
}
