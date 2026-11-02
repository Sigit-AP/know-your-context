// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// A contract with state, constructor and methods.
contract Counter {
    uint256 public value;
    address public owner;

    constructor(uint256 initial) {
        value = initial;
        owner = msg.sender;
    }

    function bump() public returns (uint256) {
        value += 1;
        return value;
    }

    function reset() external onlyOwner {
        value = 0;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }
}
